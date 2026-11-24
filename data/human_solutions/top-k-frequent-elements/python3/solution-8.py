import heapq
from collections import Counter

def topKFrequent(nums, k):
    counter = Counter(nums)
    heap = []
    
    for num, freq in counter.items():
        if len(heap) < k:
            heapq.heappush(heap, (freq, num))
        else:
            heapq.heappushpop(heap, (freq, num))
    
    return [num for freq, num in heap]

# Example usage
nums = [1, 1, 1, 2, 2, 3]
k = 2
result = topKFrequent(nums, k)
print(result)  # Output will be [1, 2]