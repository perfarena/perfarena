import heapq

def heaviest_stone_weight(stones):
    max_heap = [-stone for stone in stones]  # Create a max heap by negating the weights
    heapq.heapify(max_heap)  # Convert the list into a heap

    while len(max_heap) > 1:
        heaviest1 = -heapq.heappop(max_heap)  # Remove the heaviest stone
        heaviest2 = -heapq.heappop(max_heap)  # Remove the second heaviest stone

        diff = heaviest1 - heaviest2
        if diff > 0:
            heapq.heappush(max_heap, -diff)  # Push the difference back into the heap

    return -max_heap[0] if max_heap else 0  # Return the weight of the remaining stone or zero

# Example usage
stones = [2, 7, 4, 1, 8, 1]
result = heaviest_stone_weight(stones)
print("Weight of the heaviest stone:", result)