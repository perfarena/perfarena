import heapq

def last_stone_weight(stones):
    stones = [-stone for stone in stones]
    heapq.heapify(stones)
    
    while len(stones) > 1:
        first = heapq.heappop(stones)
        second = heapq.heappop(stones)
        
        if first != second:
            heapq.heappush(stones, first - second)
    
    return -stones[0] if stones else 0

# Test the function
stones = [5, 4, 8, 7, 2, 1, 3]
result = last_stone_weight(stones)
print(result)