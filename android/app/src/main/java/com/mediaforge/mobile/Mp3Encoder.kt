package com.mediaforge.mobile

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import com.hualee.lame.LameControl
import java.io.File

/**
 * Turns a downloaded audio file into a real MP3.
 *
 * Android has an MP3 decoder but no MP3 encoder - AOSP has never shipped one -
 * so the audio arrives as AAC in an .m4a and there is nothing on the platform
 * that can re-encode it. This decodes with the built-in AAC decoder and feeds
 * the PCM to a bundled LAME, which is the same thing ffmpeg does on the desktop.
 */
object Mp3Encoder {

    class EncodeException(message: String) : Exception(message)

    /** Encodes [inputPath] to [outputPath]. Returns the path actually written. */
    fun encode(inputPath: String, outputPath: String, bitrateKbps: Int = 192): String {
        val extractor = MediaExtractor().apply { setDataSource(inputPath) }
        var trackIndex = -1
        var format: MediaFormat? = null
        for (i in 0 until extractor.trackCount) {
            val candidate = extractor.getTrackFormat(i)
            if (candidate.getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true) {
                trackIndex = i
                format = candidate
                break
            }
        }
        val track = format ?: throw EncodeException("no audio track in the download")
        extractor.selectTrack(trackIndex)

        val mime = track.getString(MediaFormat.KEY_MIME)!!

        val codec = MediaCodec.createDecoderByType(mime)
        codec.configure(track, null, null, 0)
        codec.start()

        // The encoder must be told the rate of the PCM it is actually handed,
        // which is not always what the container advertises: HE-AAC declares a
        // base rate and the decoder emits double it once SBR is applied. Taking
        // the container's word for it yields a file at half rate and twice the
        // length, so the decoder's own output format is the authority and LAME
        // is initialised only once that is known.
        var lameReady = false
        var channels = 2

        val out = File(outputPath).also { it.parentFile?.mkdirs() }.outputStream().buffered()
        val mp3Buffer = ByteArray(1 shl 16)
        val info = MediaCodec.BufferInfo()
        var inputDone = false
        var outputDone = false

        try {
            while (!outputDone) {
                if (!inputDone) {
                    val index = codec.dequeueInputBuffer(10_000)
                    if (index >= 0) {
                        val buffer = codec.getInputBuffer(index)!!
                        val size = extractor.readSampleData(buffer, 0)
                        if (size < 0) {
                            codec.queueInputBuffer(
                                index, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputDone = true
                        } else {
                            codec.queueInputBuffer(index, 0, size, extractor.sampleTime, 0)
                            extractor.advance()
                        }
                    }
                }

                when (val index = codec.dequeueOutputBuffer(info, 10_000)) {
                    MediaCodec.INFO_TRY_AGAIN_LATER, MediaCodec.INFO_OUTPUT_BUFFERS_CHANGED -> Unit
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> Unit
                    else -> if (index >= 0) {
                        if (!lameReady) {
                            val actual = codec.outputFormat
                            val rate = runCatching {
                                actual.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                            }.getOrElse { track.getInteger(MediaFormat.KEY_SAMPLE_RATE) }
                            channels = runCatching {
                                actual.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                            }.getOrElse { 2 }.coerceAtLeast(1)
                            // quality 5 is LAME's balanced setting; 0 is best and slow
                            LameControl.init(rate, channels, rate, bitrateKbps, 5)
                            lameReady = true
                        }
                        if (info.size > 0) {
                            val buffer = codec.getOutputBuffer(index)!!
                            val pcm = ByteArray(info.size)
                            buffer.position(info.offset)
                            buffer.get(pcm)
                            val shorts = LameControl.byteArray2ShortArray(pcm)
                            val perChannel = shorts.size / channels.coerceAtLeast(1)
                            if (perChannel > 0) {
                                val written =
                                    LameControl.encodeInterleaved(shorts, perChannel, mp3Buffer)
                                if (written > 0) out.write(mp3Buffer, 0, written)
                            }
                        }
                        codec.releaseOutputBuffer(index, false)
                        if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
                            outputDone = true
                        }
                    }
                }
            }

            if (lameReady) {
                val tail = LameControl.flush(mp3Buffer)
                if (tail > 0) out.write(mp3Buffer, 0, tail)
            }
        } finally {
            runCatching { out.flush(); out.close() }
            if (lameReady) runCatching { LameControl.close() }
            runCatching { codec.stop() }
            runCatching { codec.release() }
            runCatching { extractor.release() }
        }

        val result = File(outputPath)
        if (!result.exists() || result.length() == 0L) {
            throw EncodeException("the encoder produced nothing")
        }
        return outputPath
    }
}
