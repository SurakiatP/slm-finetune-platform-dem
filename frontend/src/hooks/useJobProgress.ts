import { useEffect, useReducer, useRef } from 'react'

import type {
  HPOProgressMsg,
  JobCompletedMsg,
  JobFailedMsg,
  SDGProgressMsg,
  TrainingProgressMsg,
  WSMessage,
} from '@/api/types'
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

/**
 * Subscribe to /ws/jobs/{jobId} and accumulate progress for charts.
 *
 * The socket has no replay — callers must keep a REST polling fallback and
 * treat the REST status as authoritative (see useJobPolling).
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

    const connect = () => {
      ws = new WebSocket(jobSocketUrl(jobId))

      ws.onopen = () => {
        attempts = 0
        dispatch({ type: 'socket', open: true })
      }

      ws.onmessage = (event) => {
        const msg = parseWSMessage(String(event.data))
        if (!msg || msg.job_id !== jobId) return
        dispatch({ type: 'message', msg })
        if (isTerminalMessage(msg)) {
          stopped = true
          onTerminalRef.current?.(msg)
          ws?.close()
        }
      }

      ws.onclose = () => {
        dispatch({ type: 'socket', open: false })
        if (stopped || attempts >= MAX_RECONNECT_ATTEMPTS) return
        const delay = Math.min(10_000, 1000 * 2 ** attempts)
        attempts += 1
        reconnectTimer = window.setTimeout(connect, delay)
      }

      ws.onerror = () => {
        ws?.close()
      }
    }

    connect()

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
