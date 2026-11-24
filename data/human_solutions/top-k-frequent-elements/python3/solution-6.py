def topKFrequent(nums, k):
    count = {}
    frequency = [[] for _ in range(len(nums) + 1)]
    
    for n in nums:
        count[n] = count.get(n, 0) + 1
    
    for key, value in count.items():
        frequency[value].append(key)
    
    result = []
    for i in range(len(nums), 0, -1):
        if frequency[i]:
            result.extend(frequency[i])
        if len(result) >= k:
            break
    
    return result[:k]