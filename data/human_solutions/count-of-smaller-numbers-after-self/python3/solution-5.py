from sortedcontainers import SortedList

def countSmaller(nums):
    results = []
    sorted_list = SortedList()
    
    for i in range(len(nums)-1, -1, -1):
        num = nums[i]
        index = sorted_list.bisect_left(num)
        results.append(index)
        sorted_list.add(num)
    
    return results[::-1]