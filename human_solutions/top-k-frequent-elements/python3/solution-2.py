import heapq

def topKFrequent(nums, k):
    my_dict = {}
    
    for x in nums:
        if x in my_dict:
            my_dict[x] += 1
        else:
            my_dict[x] = 1
    
    return heapq.nlargest(k, my_dict, key=my_dict.get)

# Example usage
nums = [1, 1, 1, 2, 2, 3]
k = 2
result = topKFrequent(nums, k)
print(result)