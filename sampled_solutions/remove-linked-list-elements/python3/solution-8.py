# Actual code snippet from the method
# You can find the full code in the description below
def remove_elements(head, val):
    sentinel = ListNode(0)
    sentinel.next = head
    prev, curr = sentinel, head
    
    while curr is not None:
        if curr.val == val:
            prev.next = curr.next
        else:
            prev = curr
        curr = curr.next
        
    return sentinel.next