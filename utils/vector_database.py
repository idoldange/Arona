import os
import uuid
import asyncio
from datetime import datetime
from typing import List, Optional, Union

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
import torch
from config import VECTOR_DB_PATH, EMBEDDING_MODEL, INFERENCE_DEVICE
from console import console

# INTEL XEON 4214R HARDWARE OPTIMIZATION
# 12 Physical Cores. Limiting threads prevents core contention and overhead from Hyper-Threading.
os.environ["OMP_NUM_THREADS"] = "12"
os.environ["MKL_NUM_THREADS"] = "12"
# KMP_AFFINITY ensures computation threads stay close to physical cores to leverage L3 Cache.
os.environ["KMP_AFFINITY"] = "granularity=fine,compact,1,0"
class AronaRAG:
    def __init__(self, db_path: str = VECTOR_DB_PATH):
        """
        Initializes the RAG system with optimized settings for Cascade Lake architecture.
        Maintains full backward compatibility with previous versions.
        """
        # 1. ChromaDB Configuration (Enhanced SQLite performance via Persistent Storage)
        console.log(f"Initializing ChromaDB at {db_path}...", "INFO")
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(anonymized_telemetry=False, is_persistent=True)
        )
        self.collection = self.client.get_or_create_collection("arona_memories")
        console.log(f"ChromaDB initialized successfully.", "INFO")
        # 2. Load BGE-M3 Model
        console.log(f"Loading embedding model {EMBEDDING_MODEL} on {INFERENCE_DEVICE}...", "INFO")
        self.device = INFERENCE_DEVICE
        self.model = SentenceTransformer(EMBEDDING_MODEL, device=self.device)
        self.model.eval()

        # Set low-level PyTorch execution threads to match Xeon's physical core count
        torch.set_num_threads(12)
        console.log("Embedding model loaded and ready for inference.", "INFO")

    def _validate_input(self, content: str):
        """Validates input to prevent runtime errors or resource waste."""
        if not content or not isinstance(content, str) or len(content.strip()) == 0:
            raise ValueError("Content cannot be empty or invalid format.")

    async def add_to_memory(self, content: str) -> str:
        """
        Adds content to the vector database.
        Runs encoding in a ThreadPool to prevent blocking the event loop.
        """
        try:
            self._validate_input(content)
            
            loop = asyncio.get_running_loop()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            doc_id = str(uuid.uuid4())

            # Encode using executor to utilize multi-core without freezing the Event Loop.
            # normalize_embeddings is crucial for BGE-M3 accuracy.
            vector = await loop.run_in_executor(
                None, 
                lambda: self.model.encode(
                    [content], 
                    normalize_embeddings=True,
                    batch_size=1
                )[0].tolist()
            )

            self.collection.add(
                ids=[doc_id],
                embeddings=[vector],
                documents=[content],
                metadatas=[{"timestamp": timestamp}]
            )
            console.log(f"Content added to memory with ID: {doc_id}", "INFO")
            return f"Successfully saved at {timestamp}"
        except Exception as e:
            return f"Error adding to memory: {str(e)}"

    async def query_memory(self, query: str, n_results: int = 3) -> str:
        """
        Queries relevant information from vector storage.
        Returns a formatted string compatible with downstream LLM processing.
        """
        try:
            self._validate_input(query)
            
            loop = asyncio.get_running_loop()
            query_vector = await loop.run_in_executor(
                None, 
                lambda: self.model.encode([query], normalize_embeddings=True)[0].tolist()
            )

            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=min(n_results, 10) # Cap results to prevent memory bloat
            )

            if not results['documents'] or not results['documents'][0]:
                return "No relevant information found in memory."

            output = []
            for i in range(len(results['documents'][0])):
                doc_id = results['ids'][0][i]
                text = results['documents'][0][i]
                metadata = results['metadatas'][0][i]
                timestamp = metadata.get('timestamp', 'Unknown Time')
                
                # Format: ID | [Timestamp]: Content
                output.append(f"ID: {doc_id} | [{timestamp}]: {text}")

            return "\n".join(output)
        except Exception as e:
            return f"Query failed: {str(e)}"

    async def delete_from_memory(self, doc_id: str) -> str:
        """Removes a specific memory entry by ID."""
        try:
            if not doc_id:
                return "Invalid ID"
            self.collection.delete(ids=[doc_id])
            return f"Deleted memory with ID: {doc_id}"
        except Exception as e:
            return f"Delete failed: {str(e)}"

# Singleton Instance for global access
rag_engine = AronaRAG()