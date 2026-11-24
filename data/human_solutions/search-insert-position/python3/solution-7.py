left = 0
right = len(nums) - 1

while left <= right:
    mid = (left + right) // 2
    
    if nums[mid] == target:
        return mid
    elif nums[mid] < target:
        left = mid + 1
    else:
        right = mid - 1

# If target not found, return the left pointer as the index to insert
return left