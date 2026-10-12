import heapq

def lastStoneWeight(stones):
    # Multiply each stone weight by -1 to simulate a max heap
    stones = [-stone for stone in stones]
    heapq.heapify(stones)

    while len(stones) > 1:
        x = -heapq.heappop(stones)
        y = -heapq.heappop(stones)

        if x != y:
            heapq.heappush(stones, -(x - y))

    return -stones[0] if stones else 0

# Example usage
stones = [2, 7, 4, 1, 8]
print(lastStoneWeight(stones))  # Output: 1