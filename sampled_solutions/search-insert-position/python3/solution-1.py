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
    return L

# Test the function
nums = [1, 3, 5, 6]
target = 5
print(searchInsert(nums, target))  # Output: 2