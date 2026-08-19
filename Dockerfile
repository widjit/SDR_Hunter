# SDR Hunter — web-mode-only container image
#
# This image runs ONLY the headless FastAPI web server (`python main.py --web`).
# It does NOT include the PyQt6 desktop GUI / VNC — for the desktop app run the
# project natively (see README.md / QUICKSTART.md).
#
# SoapySDR + common driver modules are installed from Debian apt so real SDRs
# work on Linux hosts (with USB passthrough — see DOCKER.md). With no hardware
# the app falls back to a synthetic mock device (SDRHUNTER_FORCE_MOCK=1, the
# default below).
#
#   docker build -t sdr-hunter-web .
#   docker run --rm -p 8000:8000 sdr-hunter-web

FROM debian:bookworm-slim

# ---- System dependencies -------------------------------------------------
# python3 + venv + pip, curl (HEALTHCHECK), and the SoapySDR stack. The Soapy
# Python bindings (python3-soapysdr) come from apt — NOT pip — and are exposed
# to the venv below via --system-site-packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        curl \
        ca-certificates \
        soapysdr-tools \
        python3-soapysdr \
        soapysdr-module-rtlsdr \
        soapysdr-module-hackrf \
        soapysdr-module-bladerf \
        soapysdr-module-uhd \
        rtl-sdr \
        hackrf \
        libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ---- Python virtualenv (web deps only) -----------------------------------
# --system-site-packages so the venv can import the apt-installed SoapySDR
# bindings (which are not available on PyPI). The desktop Qt stack is
# deliberately excluded — see requirements-web.txt.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv --system-site-packages "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements-web.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-web.txt

# ---- Application ---------------------------------------------------------
COPY . .

# ---- Runtime user & data dir --------------------------------------------
# App state (config, recordings, baselines, sqlite db) lives under
# ~/.sdr_hunter — create it for a non-root user and make it a mountable volume.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /home/appuser/.sdr_hunter \
    && chown -R appuser:appuser /app /home/appuser
USER appuser

# ---- Environment ---------------------------------------------------------
ENV SDRHUNTER_FORCE_MOCK=1 \
    QT_QPA_PLATFORM=offscreen \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    PYTHONUNBUFFERED=1

VOLUME ["/home/appuser/.sdr_hunter"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/status || exit 1

CMD ["python", "main.py", "--web", "--host", "0.0.0.0", "--port", "8000"]
