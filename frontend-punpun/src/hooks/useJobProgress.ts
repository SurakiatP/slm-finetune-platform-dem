import { useEffect, useReducer, useRef } from 'react'

import type {
  EvaluationProgressMsg,
  ExportProgressMsg,
  HPOProgressMsg,
  JobCompletedMsg,
  JobFailedMsg,
  SDGProgressMsg,
  TrainingProgressMsg,
  WSMessage,
} from '@/api/types'
import { getJobProgress } from '@/api/endpoints/jobs'
import { isTerminalMessage, jobSocketUrl, parseWSMessage } from '@/api/ws'

export interface LossPoint {
  step: number
  train_loss: number | null
  eval_loss: number | null
}

export interface TrialPoint {
  trial_number: number
  value: number | null
  pruned: boolean
  params: Record<string, string | number | boolean> | null
}

export interface JobProgressState {
  /** Most recent message of each kind — null until first seen. */
  sdg: SDGProgressMsg | null
  training: TrainingProgressMsg | null
  hpo: HPOProgressMsg | null
  exportProgress: ExportProgressMsg | null
  evaluationProgress: EvaluationProgressMsg | null
  completed: JobCompletedMsg | null
  failed: JobFailedMsg | null
  /** Accumulated chart series (manual training, or inner progress of HPO trials). */
  lossHistory: LossPoint[]
  /** One entry per finished HPO trial. */
  trials: TrialPoint[]
  socketOpen: boolean
}

const initialState: JobProgressState = {
  sdg: null,
  training: null,
  hpo: null,
  exportProgress: null,
  evaluationProgress: null,
  completed: null,
  failed: null,
  lossHistory: [],
  trials: [],
  socketOpen: false,
}

type Action = { type: 'message'; msg: WSMessage } | { type: 'socket'; open: boolean } | { type: 'reset' }

function appendLoss(history: LossPoint[], msg: TrainingProgressMsg): LossPoint[] {
  if (msg.train_loss === null && msg.eval_loss === null) return history
  const last = history[history.length - 1]
  if (last && last.step === msg.step) {
    // Same step reported twice (e.g. train then eval) — merge instead of duplicating.
    const merged: LossPoint = {
      step: msg.step,
      train_loss: msg.train_loss ?? last.train_loss,
      eval_loss: msg.eval_loss ?? last.eval_loss,
    }
    return [...history.slice(0, -1), merged]
  }
  return [...history, { step: msg.step, train_loss: msg.train_loss, eval_loss: msg.eval_loss }]
}

function reducer(state: JobProgressState, action: Action): JobProgressState {
  if (action.type === 'socket') return { ...state, socketOpen: action.open }
  if (action.type === 'reset') return initialState

  const msg = action.msg
  switch (msg.type) {
    case 'sdg_progress':
      return { ...state, sdg: msg }
    case 'training_progress':
      return { ...state, training: msg, lossHistory: appendLoss(state.lossHistory, msg) }
    case 'hpo_progress': {
      let trials = state.trials
      // A new trial number with a recorded last_trial_value closes the previous trial.
      if (msg.last_trial_value !== null || msg.last_trial_pruned) {
        const prevNumber = msg.trial_number - 1
        if (prevNumber >= 0 && !trials.some((t) => t.trial_number === prevNumber)) {
          trials = [
            ...trials,
            {
              trial_number: prevNumber,
              value: msg.last_trial_value,
              pruned: msg.last_trial_pruned,
              params: null,
            },
          ]
        }
      }
      const lossHistory = msg.inner_progress
        ? appendLoss(state.lossHistory, msg.inner_progress)
        : state.lossHistory
      return { ...state, hpo: msg, trials, lossHistory }
    }
    case 'export_progress':
      return { ...state, exportProgress: msg }
    case 'evaluation_progress':
      return { ...state, evaluationProgress: msg }
    case 'completed':
      return { ...state, completed: msg }
    case 'failed':
      return { ...state, failed: msg }
  }
}

interface UseJobProgressOptions {
  /** Pause the socket entirely (e.g. job already terminal per REST). */
  enabled?: boolean
  /** Called once when a completed/failed frame arrives — invalidate caches here. */
  onTerminal?: (msg: JobCompletedMsg | JobFailedMsg) => void
}

const MAX_RECONNECT_ATTEMPTS = 6

/** `VITE_MOCK=1` has no backend to open a real socket against — the REST
 *  snapshot fetch below (`getJobProgress`) is the only source of progress,
 *  so the socket is skipped entirely rather than retrying against nothing. */
const isMockMode = import.meta.env.VITE_MOCK === '1'

/**
 * Subscribe to /ws/jobs/{jobId} and accumulate progress for charts.
 *
 * The socket has no replay — callers must keep a REST polling fallback and
 * treat the REST status as authoritative.
 *
 * No auth system on this build: the socket connects with no subprotocol at
 * all (see api/ws.ts / api/client.ts — byte-identical to the pre-auth
 * backend contract).
 */
export function useJobProgress(jobId: string | null, options: UseJobProgressOptions = {}): JobProgressState {
  const { enabled = true, onTerminal } = options
  const [state, dispatch] = useReducer(reducer, initialState)
  const onTerminalRef = useRef(onTerminal)
  useEffect(() => {
    onTerminalRef.current = onTerminal
  }, [onTerminal])

  useEffect(() => {
    dispatch({ type: 'reset' })
    if (!jobId || !enabled) return

    let ws: WebSocket | null = null
    let attempts = 0
    let reconnectTimer: number | undefined
    let stopped = false
    // Set before dispatching any live WS frame — guards the snapshot fetch
    // below from clobbering newer state if it resolves after the socket has
    // already delivered a message.
    let liveFrameSeen = false
    // Consecutive closes that never saw onopen. A real network flap tends to
    // succeed at least once in between drops; a socket that can never open
    // (bad URL, server down) never does. Give up after 3 rather than
    // burning all MAX_RECONNECT_ATTEMPTS on a socket that can never open.
    let handshakeFailures = 0

    const connect = () => {
      const socket = new WebSocket(jobSocketUrl(jobId))
      ws = socket
      let opened = false

      socket.onopen = () => {
        opened = true
        attempts = 0
        handshakeFailures = 0
        dispatch({ type: 'socket', open: true })
      }

      socket.onmessage = (event) => {
        const msg = parseWSMessage(String(event.data))
        if (!msg || msg.job_id !== jobId) return
        liveFrameSeen = true
        dispatch({ type: 'message', msg })
        if (isTerminalMessage(msg)) {
          stopped = true
          onTerminalRef.current?.(msg)
          socket.close()
        }
      }

      socket.onclose = () => {
        dispatch({ type: 'socket', open: false })
        if (!opened) {
          handshakeFailures += 1
          if (handshakeFailures >= 3) return
        }
        if (stopped || attempts >= MAX_RECONNECT_ATTEMPTS) return
        const delay = Math.min(10_000, 1000 * 2 ** attempts)
        attempts += 1
        reconnectTimer = window.setTimeout(connect, delay)
      }

      socket.onerror = () => {
        socket.close()
      }
    }

    // No backend to open a socket against in mock mode — REST polling of the
    // mock snapshots (below, and each page's own refetchInterval) is the only
    // source of liveness; the socket stays permanently closed instead of
    // retrying against a URL nothing is listening on.
    if (!isMockMode) connect()

    // Cold-start hydration: the WS has no replay, so on mount the panel
    // would otherwise stay blank until the next live frame. Fetch the last
    // snapshot once and seed state with it, but never let a late-resolving
    // snapshot clobber state a live frame has already updated.
    getJobProgress(jobId)
      .then((msg) => {
        if (stopped || liveFrameSeen || msg.job_id !== jobId) return
        dispatch({ type: 'message', msg })
        if (isTerminalMessage(msg)) {
          stopped = true
          onTerminalRef.current?.(msg)
          if (ws && ws.readyState === WebSocket.OPEN) ws.close()
        }
      })
      .catch(() => {})

    return () => {
      stopped = true
      window.clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [jobId, enabled])

  return state
}

/**
 * Polling cadence for the REST detail query that backs a live job view:
 * slow heartbeat while the socket is healthy, fast poll when it is not,
 * and no polling once the job is terminal.
 */
export function jobRefetchInterval(isTerminal: boolean, socketOpen: boolean): number | false {
  if (isTerminal) return false
  return socketOpen ? 15_000 : 3_000
}
