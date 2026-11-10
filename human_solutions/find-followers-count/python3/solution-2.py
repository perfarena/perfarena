def minimize_max_pair_sum(nums):
    nums.sort()
    i, j = 0, len(nums) - 1
    max_pair = 0
    while i < j:
        max_pair = max(max_pair, nums[i] + nums[j])
        i += 1
        j -= 1
    return max_pair

# Test the function
input_array = [3, 5, 2, 8, 10, 4]
result = minimize_max_pair_sum(input_array)
print(result)