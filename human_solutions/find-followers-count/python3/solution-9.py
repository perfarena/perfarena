def minimize_max_pair_sum(nums):
    nums.sort()  # Step 1: Sort the array of pairs
    n = len(nums)
    Maxi = float('-inf')

    for i in range(n // 2):  # Step 2: Iterate through the sorted array
        sum_pair = nums[i] + nums[n - i - 1]
        Maxi = max(Maxi, sum_pair)  # Step 3: Take the maximum of the sum of pairs

    return Maxi  # Step 4: Return the minimized maximum pair sum

# Example usage
nums = [35, 42, 46]
result = minimize_max_pair_sum(nums)
print(result)