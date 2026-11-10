def search_insert(nums, target):
    left, right = 0, len(nums) - 1
    
    while left <= right:
        middle = (left + right) // 2
        
        if nums[middle] == target:
            return middle
        elif target < nums[middle]:
            right = middle - 1
        else:
            left = middle + 1
    
    return left

# Example usage
nums = [1, 3, 5, 6]
target = 5
print(search_insert(nums, target))  # Output should be 2