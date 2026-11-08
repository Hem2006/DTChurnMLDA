import { useEffect, useRef, useState } from 'react'

/** Run an async call whenever `key` changes (debounced); stale responses are ignored. */
export function useApiCall(fn, key, { delay = 200, enabled = true } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: enabled })
  const seq = useRef(0)
  useEffect(() => {
    const id = ++seq.current
    if (!enabled) { setState({ data: null, error: null, loading: false }); return }
    setState(s => ({ ...s, loading: true }))
    const t = setTimeout(() => {
      fn().then(data => id === seq.current && setState({ data, error: null, loading: false }))
        .catch(error => id === seq.current && setState({ data: null, error, loading: false }))
    }, delay)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled])
  return state
}
