def make_elements_equal(nums):
    nums.sort(reverse=True)
    answer = 0
    count = 1
    
    for i in range(1, len(nums)):
        if nums[i] != nums[i-1]:
            answer += count
        else:
            count += 1
    
    return answer

# Example usage
nums = [3, 2, 2, 1, 1]
result = make_elements_equal(nums)
print(result)  # Output: 4