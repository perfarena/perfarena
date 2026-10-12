# Create max Heap array
heap = [-stone for stone in stones]

# Apply heapify function
heapq.heapify(heap)

# Run while loop
while len(heap) > 1:
    x = -heapq.heappop(heap)
    y = -heapq.heappop(heap)
    
    if x != y:
        heapq.heappush(heap, -(x - y))

# Return the remaining stone or 0
return -heap[0] if heap else 0