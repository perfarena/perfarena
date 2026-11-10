def has_cycle(head):
    if head is None:
        return False
    
    if hasattr(head, 'seen') and head.seen:
        return True
    
    head.seen = True
    return has_cycle(head.next)