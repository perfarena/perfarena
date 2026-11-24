def minPairSum(nums):
    nums.sort()
    left, right = 0, len(nums) - 1
    answer = float('-inf')

    while left < right:
        pair_sum = nums[left] + nums[right]
        answer = max(answer, pair_sum)
        left += 1
        right -= 1

    return answer