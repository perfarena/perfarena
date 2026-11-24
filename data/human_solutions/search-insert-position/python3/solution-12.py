def binarySearch(left, right, target):
    while left <= right:
        mid = (left + right) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return left

# Example usage
nums = [1, 3, 5, 6]
target = 5
result = binarySearch(0, len(nums) - 1, target)
print(result)