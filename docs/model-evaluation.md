# Korean TTS model evaluation

## Decision

The production TTS adapter uses **Fun-CosyVoice3 0.5B (2512)** behind the
worker-only `TTSModel` boundary. The stable model key is
`cosyvoice3-0.5b-2512`; the adapter version is the first 12 characters of the
pinned model revision.

| Candidate | Code / weights terms | Korean and cloning fit | Deployment finding | Decision |
|---|---|---|---|---|
| Fun-CosyVoice3 0.5B | Apache-2.0 / Apache-2.0 | Officially lists Korean and multilingual zero-shot cloning | One 5.43 GB checkpoint subset; measured at 6,708 MiB peak VRAM on the target 8 GB GPU | Adopted |
| OpenVoice V2 + MeloTTS | MIT / MIT project release | Both projects list native Korean | Two independently versioned runtimes; published setup targets Python 3.9 and has an older dependency boundary | Rejected for the first production adapter |
| XTTS-v2 | MPL-2.0 code / Coqui Public Model License weights | Korean and cloning support | Model license restricts use to non-commercial purposes unless separately licensed | Rejected as the project default |

Primary sources reviewed on 2026-10-01:

- [CosyVoice runtime, pinned commit](https://github.com/QwenAudio/CosyVoice/tree/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc)
- [Fun-CosyVoice3 model card and files, pinned revision](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/tree/29e01c4e8d000f4bcd70751be16fa94bf3d85a18)
- [CosyVoice Apache-2.0 license](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/LICENSE)
- [OpenVoice V2 repository and stated support](https://github.com/myshell-ai/OpenVoice)
- [MeloTTS repository and license](https://github.com/myshell-ai/MeloTTS)
- [XTTS-v2 model license](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt)

This is an engineering review, not legal advice. Before redistributing a
checkpoint or offering the service commercially, the release owner must
repeat the license review against the exact pinned revisions.

## Fixed Korean acceptance protocol

The committed corpus is
`backend/tests/model/fixtures/korean_tts_acceptance.json`. It covers native
and Sino-Korean numbers, dates and units, batchim/liaison, dialogue prosody,
loanwords, and a multi-sentence long-form case. Thresholds were fixed before
the accepted run:

| Metric | Acceptance threshold |
|---|---:|
| Mean Korean character error rate (Whisper small transcription) | <= 0.35 |
| Mean CAM++ speaker-embedding cosine similarity | >= 0.60 |
| Median real-time factor | <= 3.00 |
| Cold model load | <= 180 s |
| Peak device VRAM above idle baseline | <= 7,600 MiB |
| Maximum clipped-sample ratio | <= 0.001 |

The reference was generated locally with the `ko-KR` Microsoft Heami system
voice from the exact transcript stored in the corpus. It contains no natural
person's biometric data. Production evaluation must still use an explicitly
consented speaker sample and keep that sample outside Git.

## Accepted measurement

Run date: 2026-10-01. Hardware: NVIDIA GeForce RTX 3050 OEM, 8,192 MiB.
Environment: WSL Ubuntu 22.04, Python 3.11, PyTorch 2.3.1+cu121, FP16,
batch/concurrency 1, no TensorRT or vLLM. Runtime and checkpoint revisions are
the pinned values linked above.

| Metric | Result | Gate |
|---|---:|---:|
| Cold load | 36.249 s | pass |
| Peak VRAM above idle baseline | 6,708 MiB | pass |
| Median RTF | 0.787 | pass |
| Mean Korean CER | 0.123 | pass |
| Mean speaker cosine | 0.889 | pass |
| Max clipped-sample ratio | 0.000 | pass |

The long-form case generated 26.48 seconds of audio at RTF 0.704 with CER
0.030 and speaker cosine 0.910. The first cold utterance had RTF 3.362 because
reference feature extraction was not cached; subsequent cases were 0.704 to
0.867. The gate uses median RTF while load latency is reported separately.

VRAM was sampled from `nvidia-smi` every 250 ms. Device use rose from an idle
860 MiB to 7,568 MiB, a 6,708 MiB delta; PyTorch's allocator alone reported
4,425.437 MiB. The higher device-level delta is the published result and the
adapter's admission estimate so ONNX Runtime allocations are not hidden.

The first implementation incorrectly prefixed the target text with the
CosyVoice system prompt. The model spoke part of that English prompt and mean
CER failed at 0.507. Aligning the zero-shot call with the pinned official
example (plain target text, prefixed prompt transcript) produced the accepted
0.123 result. This regression is covered by the adapter contract test.

Reproduce the run with:

```bash
python -m pip install huggingface-hub==0.36.0
git clone https://github.com/QwenAudio/CosyVoice.git models/cosyvoice-runtime
git -C models/cosyvoice-runtime checkout 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
git -C models/cosyvoice-runtime submodule update --init --recursive --depth 1
python scripts/download_cosyvoice3.py
python scripts/verify_cosyvoice3.py
python scripts/benchmark_cosyvoice3.py \
  --reference /secure/consented-reference.wav
```

Generated WAV files and `report.json` live under `models/acceptance/` and are
ignored by Git. A sanitized summary of the accepted run is committed at
`docs/evidence/cosyvoice3-rtx3050-2026-10-01.json`.

## Provenance and release duties

- Never commit model weights or voice samples.
- Download only revision `29e01c...` and deploy runtime commit `074ca6d...`.
- Preserve `voice-model-manifest.json`; it records every downloaded file's
  SHA-256 and byte length. The adapter rejects a mismatched model/runtime
  revision before allocating the model.
- Record model key/version in every output's provenance metadata.
- Keep `wetext` out of the inference image. The pinned upstream runtime tries
  to download its normalization assets from a moving `master` revision during
  model construction; this Korean prompt path uses the checkpoint tokenizer.
- Re-run the corpus after any runtime, checkpoint, CUDA, precision, tokenizer,
  or dependency change. Never silently update a pin.
- The API image must remain free of PyTorch, ONNX Runtime, CosyVoice, and model
  weights. Only `Dockerfile.worker-cosyvoice` contains the heavy runtime.
