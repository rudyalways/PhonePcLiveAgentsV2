/**
 * OpenAI-wire-compatible realtime transport (Qwen, OpenAI, MiniMax).
 */

import { OpenAIRealtimeTransport } from 'bodhi-realtime-agent';
import type { LLMTransport } from 'bodhi-realtime-agent';
import type { RealtimeConfig } from './types.js';

export function buildOpenAICompatTransport(config: RealtimeConfig): LLMTransport {
	// bodhi OpenAIRealtimeTransport reads these env vars internally.
	process.env.OPENAI_API_KEY = config.apiKey;
	if (config.baseUrl) {
		process.env.OPENAI_BASE_URL = config.baseUrl;
	}

	const transportOpts: ConstructorParameters<typeof OpenAIRealtimeTransport>[0] = {
		apiKey: config.apiKey,
		model: config.model,
		voice: config.voice,
	};

	if (config.provider === 'qwen') {
		transportOpts.transcriptionModel = config.transcriptionModel;
		transportOpts.turnDetection = config.turnDetection as Record<string, unknown> | undefined;
	}

	const transport = new OpenAIRealtimeTransport(transportOpts);
	return config.provider === 'qwen' ? patchQwenDashScopeTransport(transport, config) : transport;
}

function patchQwenDashScopeTransport(transport: OpenAIRealtimeTransport, config: RealtimeConfig): LLMTransport {
	const t = transport as any;
	const origBuildSessionConfig = t.buildSessionConfig?.bind(t);
	if (origBuildSessionConfig) {
		t.buildSessionConfig = () => {
			const session = origBuildSessionConfig();
			const audio = session.audio || {};
			const input = audio.input || {};
			const output = audio.output || {};
			delete session.audio;
			session.type = 'realtime';
			session.modalities = ['text', 'audio'];
			session.input_audio_format = 'pcm';
			session.output_audio_format = 'pcm';
			session.sample_rate = config.capabilities.inputSampleRate;
			session.voice = output.voice || config.voice;
			session.turn_detection = input.turn_detection || config.turnDetection;
			if (input.transcription) session.input_audio_transcription = input.transcription;
			const mot = session.max_output_tokens;
			if (mot === 'inf' || mot === Infinity) {
				session.max_output_tokens = Number(process.env.QWEN_MAX_OUTPUT_TOKENS || '16384');
			}
			return session;
		};
	}

	function wireDashScopeEvents() {
		const rt = t.rt;
		if (!rt || rt.__sutandoQwenDashScopeWired) return;
		rt.__sutandoQwenDashScopeWired = true;
		rt.on('response.audio.delta', (event: any) => {
			if (t._suppressAudio) return;
			const delta = event.delta || event.audio;
			if (!delta) return;
			t.onAudioOutput?.(delta);
			const bytes = Buffer.from(delta, 'base64').length;
			t.audioOutputMs = (t.audioOutputMs || 0) + (bytes / 2 / config.capabilities.outputSampleRate) * 1000;
		});
		rt.on('response.audio_transcript.delta', (event: any) => {
			const delta = event.delta || event.transcript || event.text;
			if (delta) t.onOutputTranscription?.(delta);
		});
		rt.on('response.text.delta', (event: any) => {
			const delta = event.delta || event.text;
			if (delta) t.onOutputTranscription?.(delta);
		});
		console.log(`${new Date().toISOString().slice(11, 23)} [QwenCompat] wired DashScope short realtime events`);
	}

	const origConnect = t.connect.bind(t);
	t.connect = async (...args: unknown[]) => {
		const result = await origConnect(...args);
		wireDashScopeEvents();
		return result;
	};

	const origSendContent = t.sendContent.bind(t);
	let delayedSend: NodeJS.Timeout | null = null;
	t.sendContent = (turns: unknown[], turnComplete = true) => {
		if (!turnComplete || !t._isModelGenerating || !t.rt) {
			origSendContent(turns, turnComplete);
			return;
		}
		try { t.rt.send({ type: 'response.cancel' }); } catch {}
		t._isModelGenerating = false;
		if (delayedSend) clearTimeout(delayedSend);
		delayedSend = setTimeout(() => {
			delayedSend = null;
			origSendContent(turns, turnComplete);
		}, 500);
	};

	return transport;
}
