def find_intersection(nums1, nums2):
    nums1.sort()
    nums2.sort()
    
    i, j = 0, 0
    intersection = []
    
    while i < len(nums1) and j < len(nums2):
        if nums1[i] == nums2[j]:
            intersection.append(nums1[i])
            i += 1
            j += 1
        elif nums1[i] < nums2[j]:
            i += 1
        else:
            j += 1
    
    result = []
    for num in intersection:
        result.append(num)
    
    return result