def intersection(nums1, nums2):
    nums1_set = set(nums1)
    answer = set()
    
    for num in nums2:
        if num in nums1_set and num not in answer:
            answer.add(num)
    
    return list(answer)