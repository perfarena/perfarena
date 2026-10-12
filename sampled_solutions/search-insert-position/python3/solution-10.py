def search_insert_position(nums, target):
    val = 0
    for i in range(len(nums)):
        if nums[i] == target:
            val = i
            break
        elif nums[i] > target:
            val = i
            break
    else:
        val = len(nums)
    return val

# Example usage
nums = [1, 3, 5, 6, 7]
target = 2
result = search_insert_position(nums, target)
print(result)  # Output: 1