import heapq

def smash_vodkas(weights):
    heap = [-weight for weight in weights]
    heapq.heapify(heap)
    
    while len(heap) > 1:
        x = -heapq.heappop(heap)
        y = -heapq.heappop(heap)
        
        if x > y:
            heapq.heappush(heap, -(x - y))
    
    return -heap[0] if heap else 0

# Test the function
weights = [5, 7, 3, 2]
result = smash_vodkas(weights)
print(result)  # Output should be 1