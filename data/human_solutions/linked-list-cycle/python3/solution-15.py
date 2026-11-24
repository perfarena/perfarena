def hasCycle(head):
    if not head:
        return False
    
    seen = set()
    while head:
        if head in seen:
            return True
        seen.add(head)
        head = head.next
    
    return False