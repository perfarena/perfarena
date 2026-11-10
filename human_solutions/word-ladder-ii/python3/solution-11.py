for interval in intervals:
    if pq and pq[0] <= interval[0]:
        heapq.heappop(pq)
    heapq.heappush(pq, interval[1])