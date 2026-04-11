# Changelog

All notable changes to this project will be documented in this file.

## [v1.1.2]
- **Asynchronous Data Pipelines**: Restructured Phase 2 transcoding loop into a dynamic ThreadPool queue block. Raw audio will sequentially stream and unpack into MP3 forms to exactly intercept into USB payload transfers flawlessly without bottle-necking.
- **Multicore TTS VoiceOver**: Shifted the voiceover text-generation system into an independent multicore processor payload. 200 VoiceOvers generate in seconds.
- **Metadata Analytics**: The user UI panel natively scrapes pure byte lengths from the mp3/m4a frames using `Mutagen` to guarantee mathematical disk allocation estimates flawlessly. 
- **Teritiary Loading Logic**: Added three explicit progress-bar handlers explicitly distinguishing pure Transcoding from Copying and VoiceOver payloads.

## [v1.1.1]
- **Audio Output**: Introduced AAC (VBR Optimized) algorithm mapping targeting heavily reduced physical storage requirements.
- **Space Math**: The Transcoding UI Space Dashboard implicitly calculates the exact volume of bytes on the iPod scheduled for overwrite.
- **UI Metrics**: Added a "Freed:" row mapping the overwritten bytes, making mathematical size scaling visually transparent.
- **Progress Algorithm**: The progress-bar percentage metric is now flawlessly connected to the `After` estimation.
- **UI Layout**: Fixed formatting field clipping in the Transcoder settings block.
- **Subprocessing Bugfix**: Safely silenced a widespread CP1252 byte-mapping thread crash in Python on Windows when fetching FFmpeg data.

## [v1.1]
- Fixed multi-language capabilities in a single music title voiceover.
- Fixed voiceover file estimation.
