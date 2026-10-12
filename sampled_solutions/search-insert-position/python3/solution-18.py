# Initialize pointers
left = 0
right = len(nums) - 1

# Binary search algorithm
while left <= right:
    mid = left + (right - left) // 2
    if nums[mid] == target:
        return mid
    elif target < nums[mid]:
        right = mid - 1
    else:
        left = mid + 1

# Return the index position left
return left