import heapq

def lastStoneWeight(stones):
    pq = [-stone for stone in stones]
    heapq.heapify(pq)

    while len(pq) > 1:
        x = -heapq.heappop(pq)
        y = -heapq.heappop(pq)

        if x != y:
            heapq.heappush(pq, -(x - y))

    return -pq[0] if pq else 0

# Example usage
stones = [2, 7, 4, 1, 8]
print(lastStoneWeight(stones))  # Output: 1