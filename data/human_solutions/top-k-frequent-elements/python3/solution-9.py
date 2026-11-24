from collections import Counter

def topKFrequent(nums, k):
    counter = Counter(nums)
    n = len(nums)
    buckets = [[] for _ in range(n + 1)]
    
    for num, freq in counter.items():
        buckets[freq].append(num)
    
    result = []
    for i in range(n, 0, -1):
        result.extend(buckets[i])
        if len(result) >= k:
            break
    
    return result[:k]

# Example usage
nums = [1, 1, 1, 2, 2, 3]
k = 2
result = topKFrequent(nums, k)
print(result)  # Output will be [1, 2]