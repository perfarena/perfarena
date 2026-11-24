def searchInsert(nums, target):
    L, R = 0, len(nums) - 1
    
    while L <= R:
        mid = (L + R) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            L = mid + 1
        else:
            R = mid - 1
    
    return L  # Return the left index for insertion

# Example usage
nums = [1, 3, 5, 6]
target1 = 7
target2 = 3
print(searchInsert(nums, target1))  # Output: 4
print(searchInsert(nums, target2))  # Output: 1