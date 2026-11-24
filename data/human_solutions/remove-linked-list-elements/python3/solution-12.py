class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

def remove_elements(head, val):
    dummy = ListNode(0)
    dummy.next = head
    previous = dummy
    current = head

    while current:
        if current.val == val:
            previous.next = current.next
        else:
            previous = previous.next
        current = current.next

    return dummy.next