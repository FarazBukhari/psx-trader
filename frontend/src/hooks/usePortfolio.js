/**
 * usePortfolio — mounts portfolio fetch on first call, returns store state.
 */

import { useEffect } from 'react'
import { usePortfolioStore } from '../store/usePortfolioStore'

export function usePortfolio() {
  const fetch     = usePortfolioStore((s) => s.fetch)
  const portfolio = usePortfolioStore((s) => s.portfolio)
  const loading   = usePortfolioStore((s) => s.loading)
  const error     = usePortfolioStore((s) => s.error)

  useEffect(() => {
    fetch()
  }, [fetch])

  return { portfolio, loading, error, refetch: fetch }
}
