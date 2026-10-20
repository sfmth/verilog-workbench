import {parseVCD} from './vcd.mjs';

self.onmessage = ({data}) => {
  try {
    self.postMessage({ok: true, waveform: parseVCD(data?.text)});
  } catch (error) {
    self.postMessage({ok: false, error: error instanceof Error ? error.message : 'Unable to parse VCD.'});
  }
};
