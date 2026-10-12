from collections import Counter
import heapq

def topKFrequent(nums, k):
    count_dict = Counter(nums)
    heap = [(-count, num) for num, count in count_dict.items()]
    heapq.heapify(heap)
    
    result = []
    for _ in range(k):
        result.append(heapq.heappop(heap)[1])
    
    return result

# Example usage
input_array = [1, 1, 1, 2, 2, 3]
K = 2
output = topKFrequent(input_array, K)
print(output)  # Output will be [1, 2]