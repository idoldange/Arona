import os
from functools import lru_cache

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "database", "skills")
SKILLS_DIR = os.path.abspath(SKILLS_DIR)


def list_skills() -> list[dict]:
    """Return list of available skills as [{name, path}]."""
    if not os.path.isdir(SKILLS_DIR):
        return []
    skills = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        skill_path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if os.path.isfile(skill_path):
            skills.append({"name": name, "path": skill_path})
    return skills


@lru_cache(maxsize=32)
def read_skill(name: str) -> str | None:
    """Read and return the content of a SKILL.md by skill name. Returns None if not found."""
    skill_path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(skill_path):
        return None
    with open(skill_path, "r", encoding="utf-8") as f:
        return f.read()


def skill_exists(name: str) -> bool:
    """Check if a skill exists."""
    return os.path.isfile(os.path.join(SKILLS_DIR, name, "SKILL.md"))


def get_skill_description(name: str) -> str | None:
    """
    Extract the 'description' field from the YAML front matter of a SKILL.md.
    Returns None if skill not found or no description field.
    """
    content = read_skill(name)
    if not content:
        return None
    in_frontmatter = False
    desc_lines = []
    collecting = False
    for line in content.splitlines():
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                break  # end of frontmatter
        if not in_frontmatter:
            continue
        if collecting:
            # Multi-line description (indented continuation)
            if line.startswith(" ") or line.startswith("\t"):
                desc_lines.append(line.strip().strip('"'))
                continue
            else:
                break
        if line.lower().startswith("description:"):
            val = line[len("description:"):].strip().strip('"')
            desc_lines.append(val)
            collecting = True
    return " ".join(desc_lines).strip() or None


def skills_summary() -> str:
    """Return a formatted summary of all available skills for use in prompts."""
    skills = list_skills()
    if not skills:
        return "No skills available."
    lines = ["Available skills:"]
    for s in skills:
        desc = get_skill_description(s["name"]) or "(no description)"
        # Truncate long descriptions
        if len(desc) > 120:
            desc = desc[:117] + "..."
        lines.append(f"  - {s['name']}: {desc}")
    return "\n".join(lines)
