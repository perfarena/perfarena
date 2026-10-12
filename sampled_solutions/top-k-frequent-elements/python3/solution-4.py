from collections import defaultdict

def topKFrequent(nums, k):
    frequency_map = defaultdict(list)
    
    for num in nums:
        frequency_map[num].append(num)
    
    sorted_frequency_map = sorted(frequency_map.values(), key=lambda x: len(x), reverse=True)
    
    top_k_elements = [group[0] for group in sorted_frequency_map[:k]]
    
    return top_k_elements