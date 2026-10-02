import bisect, struct, sys, re, collections
syms=[]
for l in open(sys.argv[1]):
    f=l.split()
    if len(f)==3 and f[1] in 'tTwW': syms.append((int(f[0],16),f[2]))
syms.sort(); addrs=[a for a,_ in syms]
stext=[a for a,n in syms if n=='_stext'][0]
def prof(p):
    r=open(p,'rb').read(); step=struct.unpack('>I',r[:4])[0]; n=(len(r)-4)//4
    return step, struct.unpack('>%dI'%n, r[4:])
step, idle = prof(sys.argv[2])
GROUPS=[('wireguard',r'^wg_|wireguard|noise|cookie|ratelimit|allowedips'),
 ('chacha',r'chacha'),('poly1305',r'poly1305'),('crypto_xor/memneq',r'crypto_xor|memneq|scatterwalk|sg_miter'),
 ('csum',r'csum'),('memcpy/memset',r'^mem(cpy|move|set)|copy_(to|from)_user|__copy_user|__raw_copy'),
 ('driver eth',r'rtl8196e|rtl819x|^swcore|_eth_'),('netfilter',r'^nf_|^ipt_|^xt_|nf_conntrack|nf_nat|iptable|ipv4_conntrack|nf_hook'),
 ('ip/udp/tcp',r'^ip_|^ip4|^udp|^tcp|^__tcp|^inet|fib_|^raw_|^icmp|dst_'),
 ('skb/net core',r'skb|^__dev|^dev_|^netif|napi|^net_|^__netif|gro|gso|neigh|^sk_|^sock|qdisc|^__qdisc|eth_type'),
 ('sched/workqueue',r'schedule|switch|^__switch|worker|work|wake|task|^try_to|^enqueue|^dequeue|^pick_|^update_|rq_|^__queue|kthread|ptr_ring'),
 ('irq/softirq',r'irq|softirq|^do_IRQ|plat_irq|handle_'),('cache/tlb',r'cache|flush|tlb|c-lexra|lexra'),
 ('mm/slab',r'kmem|kmalloc|slab|slub|^__alloc|page|kfree|^free_|alloc_'),('timers/time',r'timer|ktime|clock|hrtimer|jiffies'),
 ('syscall/user entry',r'syscall|^sys_|^__sys|^ret_from|^handle_sys|resume|restore|^do_|vfs|fd'),]
def group(n):
    for g,rx in GROUPS:
        if re.search(rx,n): return g
    return 'other'
for path in sys.argv[3:]:
    st, load = prof(path)
    funcs=collections.Counter()
    for i,v in enumerate(load):
        h=v-idle[i] if i<len(idle) else v
        if h<=0: continue
        a=stext+i*step; k=bisect.bisect_right(addrs,a)-1
        funcs[syms[k][1]]+=h
    tot=sum(funcs.values()) or 1
    groups=collections.Counter()
    for n,h in funcs.items(): groups[group(n)]+=h
    print('=====',path.split('/')[-1],'samples',tot)
    print('  groups:', ', '.join('%s %.1f%%'%(g,100*h/tot) for g,h in groups.most_common(12)))
    for n,h in funcs.most_common(25): print('   %5.1f%%  %-40s %s'%(100*h/tot,n,group(n)))
