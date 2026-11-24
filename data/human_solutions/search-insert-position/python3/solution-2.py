def search_insert(nums, target):
    low, high = 0, len(nums)
    
    while low < high:
        mid = (low + high) // 2
        if nums[mid] >= target:
            high = mid
        else:
            low = mid + 1
    
    return low

# Test the function
nums = [1, 3, 5, 6]
target1 = 5
target2 = 2
print(search_insert(nums, target1))  # Output: 2
print(search_insert(nums, target2))  # Output: 1