#!/usr/bin/env bash
#
# Download the exact model stack the bundled WAN 2.2 I2V 4-step workflow needs.
#
# Only six files. Nothing is written outside <ComfyUI>/models/{text_encoders,
# diffusion_models,vae,loras}.
#
#   ./scripts/fetch_wan22_i2v_models.sh /opt/ComfyUI          # download
#   ./scripts/fetch_wan22_i2v_models.sh /opt/ComfyUI --check  # report only
#
# Requires curl (works on Linux, macOS, WSL and Git Bash). Files that already
# exist with the expected byte size are skipped, so re-running resumes a partial
# download instead of starting over.

set -euo pipefail

REPO_22="Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
REPO_21="Comfy-Org/Wan_2.1_ComfyUI_repackaged"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

COMFY_ROOT=""
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) COMFY_ROOT="$arg" ;;
  esac
done

if [ -z "$COMFY_ROOT" ]; then
  echo "usage: $0 <ComfyUI root> [--check]" >&2
  exit 2
fi
if [ ! -d "$COMFY_ROOT/models" ]; then
  echo "error: $COMFY_ROOT/models not found; pass the ComfyUI install root" >&2
  exit 2
fi

# repo | split_files path | destination dir | expected bytes
FILES=(
  "$REPO_22|split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors|text_encoders|6735906897"
  "$REPO_22|split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors|diffusion_models|14294742832"
  "$REPO_22|split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors|diffusion_models|14294742832"
  "$REPO_21|split_files/vae/wan_2.1_vae.safetensors|vae|253815318"
  "$REPO_22|split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors|loras|1226977424"
  "$REPO_22|split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors|loras|1226977424"
)

total=0
missing=0
for entry in "${FILES[@]}"; do
  IFS='|' read -r repo rel dest size <<<"$entry"
  total=$((total + size))
  target="$COMFY_ROOT/models/$dest/$(basename "$rel")"
  if [ -f "$target" ]; then
    have=$(wc -c <"$target" | tr -d ' ')
    if [ "$have" = "$size" ]; then
      printf 'ok      %s\n' "$target"
      continue
    fi
    printf 'partial %s (%s / %s bytes) -> resuming\n' "$target" "$have" "$size"
  else
    printf 'missing %s\n' "$target"
  fi
  missing=$((missing + 1))
  if [ "$CHECK_ONLY" -eq 0 ]; then
    mkdir -p "$COMFY_ROOT/models/$dest"
    url="$HF_ENDPOINT/$repo/resolve/main/$rel"
    curl -L --fail --retry 3 --retry-delay 5 -C - \
      -o "$target" "$url"
    have=$(wc -c <"$target" | tr -d ' ')
    if [ "$have" != "$size" ]; then
      echo "error: $target has $have bytes, expected $size" >&2
      exit 1
    fi
  fi
done

printf '\ntotal stack size: %s bytes (~%s GiB)\n' "$total" "$((total / 1073741824))"
if [ "$CHECK_ONLY" -eq 1 ] && [ "$missing" -gt 0 ]; then
  exit 1
fi
echo "done. restart ComfyUI so the new models are indexed."
