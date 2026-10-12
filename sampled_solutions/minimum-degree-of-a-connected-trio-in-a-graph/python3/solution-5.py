def make_elements_equal(nums):
    nums.sort(reverse=True)
    count = 1
    
    for i in range(1, len(nums)):
        if nums[i] != nums[i - 1]:
            count += 1
    
    return count

# Example usage
nums = [3, 3, 3, 2, 2, 1]
answer = make_elements_equal(nums)
print(answer)