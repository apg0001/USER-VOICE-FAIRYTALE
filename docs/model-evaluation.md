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

## Korean speech VC acceptance (Phase 12)

Speech VC reuses the adopted checkpoint through the pinned runtime's
`inference_vc`, so the license and provenance review above applies unchanged.
The corpus is `backend/tests/model/fixtures/korean_vc_acceptance.json` and
runs the real `VoiceConversionPipeline` (10 s chunks) against two synthetic
targets in two directions:

| Direction | Source | Target reference |
|---|---|---|
| `heami_to_zira` | The six accepted Phase 11 outputs (Heami timbre, known Korean text) | Microsoft Zira en-US |
| `shifted_to_heami` | The same outputs pitch-shifted by -5 semitones | Microsoft Heami ko-KR |

Speaker change is measured as the gain in CAM++ cosine to the target from the
source to the converted output, so a target that already sounds like the
source cannot pass. Content preservation is Whisper-small CER against the
fixture text. Thresholds were fixed before measurement:

| Metric | Acceptance threshold |
|---|---:|
| Mean Korean CER | <= 0.35 |
| Mean target speaker cosine | >= 0.55 |
| Mean target cosine gain over source | >= 0.15 |
| Median RTF | <= 1.50 |
| Cold model load | <= 180 s |
| Peak device VRAM above idle baseline | <= 7,600 MiB |
| Maximum clipped-sample ratio | <= 0.001 |

### Accepted measurement

Run date: 2026-10-23, same hardware, environment, and pins as the TTS run.

| Metric | Result | Gate |
|---|---:|---:|
| Cold load | 135.017 s | pass |
| Peak VRAM above idle baseline | 5,418 MiB | pass |
| Median RTF | 1.056 | pass |
| Mean Korean CER | 0.162 | pass |
| Mean target speaker cosine | 0.878 | pass |
| Mean target cosine gain over source | 0.384 | pass |
| Max clipped-sample ratio | 0.000 | pass |

| Direction | Mean CER | Source→target cosine | Output→target cosine | Mean RTF |
|---|---:|---:|---:|---:|
| `heami_to_zira` | 0.160 | 0.537 | 0.855 | 2.827 |
| `shifted_to_heami` | 0.164 | 0.451 | 0.900 | 0.608 |

The host had only 1.1–2.2 GB of free RAM during this run and paged at about
3,400 pages/s while the model loaded. Cold load (135 s versus 36 s in the TTS
run) and the first direction's RTF include that paging; the first case alone
had RTF 8.08. The second direction ran warm at mean RTF 0.608, which is the
better estimate of steady-state cost. Treat the cold-load number as an upper
bound, not a regression.

CER is highest on the number cases (0.28–0.38) because Whisper writes Arabic
numerals and the Hangul-only normalization drops them; the TTS run used the
same method. Long-form inputs ran through three 10 s chunks with CER 0.045 in
both directions, so chunk boundaries did not damage content. VRAM peaked below
the TTS figure, so the shared descriptor keeps the 6,708 MiB admission
estimate. The sanitized summary is
`docs/evidence/cosyvoice3-speech-vc-rtx3050-2026-10-23.json`.

The first attempt on 2026-10-23 was stopped: with under 1.2 GB of free host RAM
the model took about six minutes to load. Run with at least 6 GB of free host
RAM and no other process holding the GPU:

```bash
# Zira target reference: Windows SAPI, 24 kHz mono 16-bit, text from the fixture
python scripts/benchmark_cosyvoice3_vc.py --model ~/cosyvoice3-0.5b-2512 --runtime ~/cosyvoice-runtime
```

The script expects `models/acceptance/synthetic-heami-ko-kr-v1.wav`,
`models/acceptance/vc/synthetic-zira-en-us-v1.wav`, and the Phase 11 outputs
in `models/acceptance/cosyvoice3/`, and writes `models/acceptance/vc/report.json`.

## Vocal separation acceptance (Phase 13)

The singing pipeline's separator is **Demucs v4 htdemucs** (`demucs==4.1.0`,
weights `955717e8-8726e21a.th`, SHA-256 `8726e21a…aa8b4`), adapter key
`demucs-htdemucs-4.1.0`.

**License status: research / non-commercial only.** The Demucs code is MIT.
The weights carry no explicit license; the maintainer has described them as
research-only because of the MUSDB training-data terms, and a Hugging Face MIT
tag on a mirror is disputed and unanswered. Every public alternative reviewed
(Mel-Band/BS-RoFormer checkpoints) is equally unclear. The project owner chose
research use on 2026-10-23, so the adapter is opt-in (`ENABLE_DEMUCS=false` by
default, also in the GPU compose override) and its descriptor says so. Repeat
this review before any commercial release.

- [Demucs repository and MIT license](https://github.com/adefossez/demucs)
- [Hub discussion on the weights' MIT tag](https://huggingface.co/adefossez/HTDemucs/discussions/1)
- [MUSDB18 7 s excerpts, CC BY 4.0](https://zenodo.org/records/3270814)

The corpus is the 50 MUSDB18 **test** excerpts (7 s, 44.1 kHz stereo), which
are not in the htdemucs training set. SDR is whole-excerpt
`10·log10(‖s‖² / ‖s − ŝ‖²)` on stereo int16, instrumental = drums + bass +
other. The boundary check concatenates five excerpts (35 s) and compares
separating the whole signal against the worker's fixed 10 s chunks. Inference
uses `shifts=0` for deterministic output, which costs roughly 0.2 dB SDR versus
the CLI default. Thresholds were fixed before the run and sit below the
published ~9 dB vocal SDR to allow for short excerpts:

| Metric | Result | Gate |
|---|---:|---:|
| Median vocal SDR (50 excerpts) | 8.690 dB | >= 7.0 |
| Median instrumental SDR | 12.973 dB | >= 12.0 |
| Vocal SDR loss from 10 s chunking | 0.217 dB | <= 0.5 |
| Median RTF | 0.070 | <= 0.5 |
| Cold load | 0.736 s | <= 60 |
| Peak VRAM above idle baseline | 875 MiB | <= 3,000 |
| Max clipped-sample ratio | 0.000 | <= 0.001 |

Instrumental SDR cleared its gate by under 1 dB. Two excerpts scored near
0 dB vocal SDR: in *Motor Tapes – Shore* the vocal sits 11 dB under the mix,
while *Skelpolu – Resurrection* has an ordinary vocal level and is a genuine
miss. The 10th-percentile vocal SDR is 3.9 dB. The measured 875 MiB is the
adapter's VRAM admission estimate. Run on 2026-10-23, same hardware and
environment as above; summary in
`docs/evidence/htdemucs-separation-rtx3050-2026-10-23.json`.

```bash
python scripts/download_demucs.py --output ~/demucs-htdemucs
python scripts/benchmark_demucs.py --musdb-root ~/datasets/musdb18-7/wav --model ~/demucs-htdemucs
```

## Singing VC acceptance (Phase 14) — **not accepted**

Candidate: **Seed-VC** singing model (F0-conditioned, 44.1 kHz, v2), runtime
`Plachtaa/seed-vc@51383ef`, DiT checkpoint `Plachta/Seed-VC@257283f`, with
RMVPE (MIT), CAM++ (Apache-2.0), BigVGAN v2 44 kHz (MIT) and whisper-small
(Apache-2.0) pinned by revision and SHA-256. Code and DiT weights are
**GPL-3.0** and upstream was archived on 2025-11-21; the project owner chose
research use. The adapter keeps the source F0 (`auto_f0_adjust` off) and uses
30 diffusion steps.

Sources are MUSDB18 test `vocals.wav` stems with at least 30 % voiced frames
(47 tracks), converted to the two synthetic targets (94 cases). Verifiers are
deliberately independent of the model's own encoders: Resemblyzer GE2E for
speaker similarity and WORLD harvest for pitch. RPA50 is the share of
source-voiced frames whose output F0 is within 50 cents. Thresholds were fixed
and committed (`e1806b4`) before the run.

| Metric | Result | Gate |
|---|---:|---:|
| Mean target cosine gain over source | 0.284 | >= 0.10 pass |
| Mean RPA50 (melody kept within 50 cents) | **0.638** | >= 0.75 **fail** |
| Median RTF (7 s vocal) | 0.787 | <= 2.0 pass |
| Full pipeline RTF (Demucs + Seed-VC, 35 s, 4 chunks) | 0.852 | <= 3.0 pass |
| Pipeline frame count preserved | yes | pass |
| Cold load | 11.2 s | <= 180 pass |
| Peak VRAM, both models resident | 3,083 MiB | <= 7,600 pass |
| Max clipped-sample ratio | 0.00005 | <= 0.001 pass |

Only 21 of 94 cases reach RPA50 0.75 (median 0.666). A breakdown of eight
tracks shows the output follows the model's **own** F0 input faithfully: per
track, output RPA50 nearly equals the agreement between RMVPE and harvest on
the *unconverted* source (0.195 vs 0.248, 0.283 vs 0.326, 0.344 vs 0.340,
0.700 vs 0.704, 0.902 vs 0.907). A pitch-neutral resampling round trip of the
source scores 0.92–1.00, so the verifier itself is stable. The failures come
from RMVPE reading a different melody than harvest on stems with backing
vocals, harmonies or effects (one track: 34 % octave errors). A listener would
hear that as wrong notes, so this is a real quality limit of the model's
pitch front end, not a measurement artifact. The gate was not relaxed.

Status: the adapter and benchmark live on branch `feat/#15-real-singing-vc`
and are **not merged**; singing VC stays on the Mock in `develop`. Summary:
`docs/evidence/seed-vc-singing-rtx3050-2026-10-23.json`.

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
