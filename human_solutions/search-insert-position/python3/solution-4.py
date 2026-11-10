def searchInsert(nums, target):
    left, right = 0, len(nums) - 1
    
    while left <= right:
        middle = left + (right - left) // 2
        
        if nums[middle] == target:
            return middle
        elif nums[middle] < target:
            left = middle + 1
        else:
            right = middle - 1
    
    return left

# Example usage
nums1 = [1, 2, 3, 5, 6]
target1 = 5
print(searchInsert(nums1, target1))  # Output: 3

nums2 = [1, 3, 5, 6]
target2 = 2
print(searchInsert(nums2, target2))  # Output: 1