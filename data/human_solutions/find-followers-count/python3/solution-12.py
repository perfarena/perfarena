def minimize_max_pair_sum(nums):
    nums.sort()  # Step 1: Sort the array
    result = float('-inf')  # Initialize result to negative infinity
    n = len(nums)  # Get the length of the array

    for i in range(n // 2):  # Iterate through the array to form pairs
        pair_sum = nums[i] + nums[n - i - 1]  # Calculate the sum of the pair
        result = max(result, pair_sum)  # Update the maximum sum

    return result  # Return the minimized maximum sum of pairs

# Example usage
nums = [3, 5, 2, 3]
print(minimize_max_pair_sum(nums))  # Output: 5