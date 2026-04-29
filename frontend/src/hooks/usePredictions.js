/**
 * usePredictions — fetches predictions from the REST API.
 * Use the market store signals for embedded prediction data instead
 * when you already have the WS feed running.
 */

import { useState, useEffect } from 'react'
import { getPredictions } from '../api/predictions'

export function usePredictions(params = {}) {
  const [predictions, setPredictions] = useState([])
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState(null)

  // Stable dependency key
  const key = JSON.stringify(params)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    getPredictions(JSON.parse(key))
      .then((data) => { if (!cancelled) setPredictions(data.data || []) })
      .catch((err) => { if (!cancelled) setError(err.message || 'Failed to load predictions') })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [key])

  return { predictions, loading, error }
}
