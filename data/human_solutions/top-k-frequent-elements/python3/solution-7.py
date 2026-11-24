def topKFrequent(nums, k):
    freq_map = {}
    for num in nums:
        freq_map[num] = freq_map.get(num, 0) + 1
    
    freq_list = [[] for _ in range(len(nums) + 1)]
    for num, freq in freq_map.items():
        freq_list[freq].append(num)
    
    result = []
    for i in range(len(freq_list) - 1, 0, -1):
        for num in freq_list[i]:
            result.append(num)
            if len(result) == k:
                return result