FROM python:3.12-slim-bookworm

# 0. Switch to Singapore mirror (faster from VN)
RUN printf 'deb http://mirror.sg.gs/debian bullseye main\ndeb http://mirror.sg.gs/debian-security bullseye-security main\ndeb http://mirror.sg.gs/debian bullseye-updates main\n' > /etc/apt/sources.list

# 1. System dependencies & Fontconfig
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl wget git libssl-dev libffi-dev \
    dnsutils iputils-ping unzip zip ffmpeg procps psmisc neofetch \
    fontconfig locales proxychains4 \
    && rm -rf /var/lib/apt/lists/*
# NOTE: iptables/iproute2 intentionally NOT installed here anymore.
# arona-executor no longer holds NET_ADMIN/NET_RAW - the killswitch is set up
# once inside the privileged `warp` container (see warp-killswitch.sh).

# 2. Setup Locales for Unicode support
RUN echo "en_US.UTF-8 UTF-8" > /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# 3. Install JetBrains Mono Nerd Font
RUN mkdir -p /usr/share/fonts/truetype/jetbrains \
    && curl -fLo "JetBrainsMono.zip" https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip \
    && unzip JetBrainsMono.zip -d /usr/share/fonts/truetype/jetbrains \
    && rm JetBrainsMono.zip

# 4. Set Nerd Font as System Default
RUN echo '<?xml version="1.0"?>\n\
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n\
<fontconfig>\n\
  <alias>\n\
    <family>serif</family>\n\
    <prefer><family>JetBrainsMono Nerd Font</family></prefer>\n\
  </alias>\n\
  <alias>\n\
    <family>sans-serif</family>\n\
    <prefer><family>JetBrainsMono Nerd Font</family></prefer>\n\
  </alias>\n\
  <alias>\n\
    <family>monospace</family>\n\
    <prefer><family>JetBrainsMono Nerd Font</family></prefer>\n\
  </alias>\n\
</fontconfig>' > /etc/fonts/local.conf && fc-cache -f -v

# 5. Build su-exec
RUN git clone --depth=1 https://github.com/ncopa/su-exec.git \
    && cd su-exec && make && mv su-exec /usr/local/bin/su-exec \
    && cd .. && rm -rf su-exec

# 6. User & Workspace Setup
RUN groupadd -g 1000 sandboxuser || true \
    && useradd -m -u 1000 -g 1000 -s /bin/bash sandboxuser || true \
    && mkdir -p /app/workdir /app/output \
    && chown -R 1000:1000 /home/sandboxuser /app \
    && chmod -R 777 /home/sandboxuser /tmp /app/workdir /app/output

# 7. Python Libraries
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    requests[socks] aiohttp[speedups] \
    pandas numpy scipy sympy \
    cryptography \
    matplotlib pillow \
    python-docx \
    pypdf reportlab \
    python-pptx \
    openpyxl xlsxwriter \
    imageio rembg[cpu,cli]

# 7b. Node.js & npm (LTS via NodeSource)
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npm@latest \
    && rm -rf /var/lib/apt/lists/*

# 7c. Sandbox-essential global npm packages (installed as root -> /usr/lib/node_modules)
RUN npm install -g \
    typescript \
    ts-node \
    tsx \
    yarn \
    pnpm

# 7d. sandboxuser npm prefix — lets sandboxuser run `npm install -g` without root.
#     All user-global packages go to ~/.npm-global instead of /usr/lib/node_modules.
RUN mkdir -p /home/sandboxuser/.npm-global \
    && chown -R 1000:1000 /home/sandboxuser/.npm-global
ENV NPM_CONFIG_PREFIX=/home/sandboxuser/.npm-global
# Expose user-global bin so installed CLIs are immediately available in PATH
ENV PATH=/home/sandboxuser/.npm-global/bin:$PATH

# Set default font for Matplotlib
RUN mkdir -p /home/sandboxuser/.config/matplotlib && \
    echo "font.family: JetBrainsMono Nerd Font" >> /home/sandboxuser/.config/matplotlib/matplotlibrc && \
    chown -R 1000:1000 /home/sandboxuser/.config

RUN chmod -R 755 $(python3 -c "import site; print(site.getsitepackages()[0])") 2>/dev/null || true

# Allow sandboxuser to pip install without root.
# PIP_USER=1 → installs to ~/.local (which is tmpfs /home/sandboxuser at runtime → ephemeral, acceptable for sandbox).
ENV PIP_USER=1

# 8. Networking & Proxy
ENV HTTP_PROXY=http://127.0.0.1:8888 \
    HTTPS_PROXY=http://127.0.0.1:8888 \
    NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16 \
    no_proxy=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16 \
    MPLBACKEND=Agg

# 8b. npm proxy config (npm ignores HTTP_PROXY env, needs explicit config)
# Applies to both root and sandboxuser at runtime
RUN npm config set proxy http://127.0.0.1:8888 \
    && npm config set https-proxy http://127.0.0.1:8888 \
    && npm config set noproxy "localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

# sandboxuser npm config — prefix + proxy all written to ~/.npmrc
RUN mkdir -p /home/sandboxuser \
    && su-exec sandboxuser npm config set prefix /home/sandboxuser/.npm-global \
    && su-exec sandboxuser npm config set proxy http://127.0.0.1:8888 \
    && su-exec sandboxuser npm config set https-proxy http://127.0.0.1:8888 \
    && su-exec sandboxuser npm config set noproxy "localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" \
    && chown -R 1000:1000 /home/sandboxuser

# 9. Execution
COPY executor-entrypoint.sh /usr/local/bin/executor-entrypoint.sh
RUN chmod +x /usr/local/bin/executor-entrypoint.sh

WORKDIR /app
ENTRYPOINT ["/usr/local/bin/executor-entrypoint.sh"]
CMD ["tail", "-f", "/dev/null"]