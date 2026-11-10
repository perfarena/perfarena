def search_insert_position(nums, target):
    l, r = 0, len(nums) - 1
    
    while l <= r:
        mid = (l + r) // 2
        
        if nums[mid] < target:
            l = mid + 1
        elif nums[mid] > target:
            r = mid - 1
        else:
            return mid
    
    return l

# Example usage
nums = [2, 5, 7, 9]
target = 4
position = search_insert_position(nums, target)
print(position)