class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

def removeElements(head, val):
    dummy = ListNode(0)
    dummy.next = head
    current = dummy
    previous = dummy
    
    while current.next:
        current = current.next
        if current.val == val:
            previous.next = current.next
        else:
            previous = current
    
    return dummy.next