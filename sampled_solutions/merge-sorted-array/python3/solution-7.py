insertP = m + n - 1
pointer1 = m - 1
pointer2 = n - 1

while pointer1 >= 0 and pointer2 >= 0:
    if nums1[pointer1] > nums2[pointer2]:
        nums1[insertP] = nums1[pointer1]
        pointer1 -= 1
    else:
        nums1[insertP] = nums2[pointer2]
        pointer2 -= 1
    insertP -= 1

while pointer2 >= 0:
    nums1[insertP] = nums2[pointer2]
    insertP -= 1
    pointer2 -= 1