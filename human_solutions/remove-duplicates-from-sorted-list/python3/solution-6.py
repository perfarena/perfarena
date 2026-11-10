class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

def remove_duplicates(head):
    current = head
    while current is not None:
        if current.next is not None and current.val == current.next.val:
            current.next = current.next.next
        else:
            current = current.next
    return head