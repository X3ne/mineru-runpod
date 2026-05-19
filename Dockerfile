# MinerU 2.5 on RunPod Serverless — generic PDF parsing worker.
#
# Base image: vllm/vllm-openai (recommended by MinerU upstream — bundles CUDA
# + a working vLLM that the VLM backend depends on).
#
# At runtime: handler.py listens for RunPod jobs, downloads/decodes the input
# PDF, calls MinerU's async parse, and returns the result as a base64 tarball.
#
# The MinerU 2.5 VLM model (~2.5 GB) is pre-cached at build time into the
# image (see `Pre-cache MinerU weights` step below), so cold-starts don't
# depend on HuggingFace reachability or model-download time. With RunPod
# FlashBoot + idle_timeout = 10 s the model stays in GPU memory across
# requests within the same warm container.

ARG VLLM_VERSION=v0.6.6
FROM vllm/vllm-openai:${VLLM_VERSION}

# Keep Python noise down, write cache to a single root. Override MinerU's
# default model (MinerU2.5-2509-1.2B) to the Pro variant (2604-1.2B) which
# is what this template documents and pre-caches below. Experiment to
# verify MINERU_VL_MODEL_NAME is respected by the local vlm-vllm-async-engine
# backend (docs hint it's for remote openai-server use; live build will
# confirm).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/root/.cache/huggingface \
    TRANSFORMERS_OFFLINE=0 \
    MINERU_VL_MODEL_NAME=opendatalab/MinerU2.5-Pro-2604-1.2B

# vllm-openai inherits an entrypoint that launches the OpenAI server. Override
# it so our handler can be the process.
ENTRYPOINT []

# System deps. The base image already has CUDA + Python; we only need the
# things mineru/pdf processing want at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        poppler-utils \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /worker

# Install uv (10x+ faster than pip on resolution-heavy installs like
# mineru[core,vllm], which churns through pydantic / opencv / numpy
# version conflicts with the base image). Negligible image size (~10 MB)
# in exchange for a meaningful build-time win.
RUN pip install --no-cache-dir uv

# Install MinerU + RunPod worker SDK. mineru[core,vllm] pulls the VLM-engine
# dependencies that match the vllm version in the base image.
COPY requirements.txt /worker/requirements.txt
RUN uv pip install --system --no-cache -r requirements.txt

# Pre-cache MinerU weights into the image. Adds ~2.5 GB to the image but
# eliminates the model download on first cold start (~60-90 s of latency
# saved per fresh worker, and removes a network dependency on huggingface.co
# at runtime). Runs after `pip install` so huggingface_hub is available;
# runs BEFORE the handler.py copy so iterating on handler code doesn't bust
# the model layer.
RUN python3 -c "from huggingface_hub import snapshot_download; \
    snapshot_download(repo_id='opendatalab/MinerU2.5-Pro-2604-1.2B')"

# Copy the worker code last so iterating on it doesn't bust the pip / model
# layers.
COPY handler.py /worker/handler.py

# Tiny fixture PDF used by the RunPod Hub validation tests (.runpod/tests.json
# references /worker/test-fixture.pdf). Tiny (<1 KB) so it adds nothing to the
# image and gives Hub a real document to round-trip on submission.
COPY .runpod/test-fixture.pdf /worker/test-fixture.pdf

# RunPod's serverless runtime invokes Python directly. `python3` is what
# vllm/vllm-openai ships on PATH; `python` is not always aliased.
CMD ["python3", "-u", "handler.py"]
