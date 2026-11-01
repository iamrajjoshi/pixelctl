"""Compile real C providers and exercise them with synthetic TLS envelopes."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


NATIVE = Path(__file__).resolve().parent
HARNESS = r'''
#define _POSIX_C_SOURCE 200809L
#include "standalone_providers.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
/* The provider object calls this instead of fork. Assert the close-on-exec
 * invariant deterministically, before another worker could inherit a writer. */
pid_t pc_checked_fork(void){
 for(int fd=3;fd<256;fd++){
  struct stat info;
  if(!fstat(fd,&info) && S_ISFIFO(info.st_mode) && !(fcntl(fd,F_GETFD)&FD_CLOEXEC)){
   fputs("HTTP pipe was inheritable at fork\n",stderr);_exit(91);
  }
 }
 return fork();
}
static char *file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *t = calloc(n + 1, 1);
    if (fread(t, 1, n, f) != (size_t)n) { free(t); t = NULL; }
    fclose(f); return t;
}
int main(int argc,char**argv){
 if(argc<3)return 2;
 pc_state*s=calloc(1,sizeof(*s));pc_config c;char error[96];
 if(!strcmp(argv[1],"collect") || !strcmp(argv[1],"cloud")){
  if(pc_provider_config_load(argv[2],&c,error,sizeof(error))<0){printf("{\"config_error\":true}");return 0;}
  if(!strcmp(argv[1],"cloud"))pc_collect_cloud(&c,s);else pc_collect(&c,s);
  printf("{\"prs\":%d,\"total\":%d,\"complete\":%d,\"checks_available\":%d,\"first_ci\":%d,\"prs_error\":%d,\"garden\":%d,\"garden_total\":%d,\"deploy\":%d,\"live\":%d,\"serving\":%d,\"health\":%d,\"samples\":%llu,\"failures\":%llu,\"unknown\":%llu,\"availability\":%.3f}",s->prs.count,s->prs.total,s->prs.complete,s->prs.checks_available,s->prs.items[0].status,s->prs.error[0]!=0,s->garden.count,s->garden.total,s->deploy.state,s->deploy.live,s->deploy.serving_state,s->health.state,(unsigned long long)s->uptime.samples,(unsigned long long)s->uptime.failures,(unsigned long long)s->uptime.unknown,s->uptime.availability_pct);
 }else if(!strcmp(argv[1],"reconfigure")){
  if(pc_provider_config_load(argv[2],&c,error,sizeof(error))<0)return 3;
  pc_probe_health(&c,s);
  if(pc_provider_config_load(argv[3],&c,error,sizeof(error))<0)return 3;
  pc_probe_health(&c,s);
  printf("{\"samples\":%llu,\"unknown\":%llu,\"availability\":%.3f}",(unsigned long long)s->uptime.samples,(unsigned long long)s->uptime.unknown,s->uptime.availability_pct);
 }else if(!strcmp(argv[1],"probe")){
  if(pc_provider_config_load(argv[2],&c,error,sizeof(error))<0)return 3;
  setenv("PC_TEST_HEALTH","ok",1);pc_probe_health(&c,s);
  setenv("PC_TEST_HEALTH",argv[3],1);pc_probe_health(&c,s);
  printf("{\"health\":%d,\"samples\":%llu,\"failures\":%llu,\"unknown\":%llu,\"availability\":%.3f}",s->health.state,(unsigned long long)s->uptime.samples,(unsigned long long)s->uptime.failures,(unsigned long long)s->uptime.unknown,s->uptime.availability_pct);
 }else if(!strcmp(argv[1],"predicate")){
  char*a=file(argv[2]),*b=file(argv[3]);printf("{\"matches\":%d}",pc_health_json_matches(a,b));free(a);free(b);
 }else if(!strcmp(argv[1],"prs")){
  char cursor[512];int result=0;
  for(int i=2;i<argc;i++){char*body=file(argv[i]);result=pc_parse_pr_page(body,&s->prs,cursor,sizeof(cursor));free(body);if(result<0)break;}
  printf("{\"result\":%d,\"count\":%d,\"total\":%d,\"status\":%d,\"review\":%d}",result,s->prs.count,s->prs.total,s->prs.items[0].status,s->prs.items[0].review);
 }
 free(s);return 0;
}
'''

FAKE_HTTPS = r'''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:];url=args[0];status=200;verified=True
if 'api.github.com/graphql' in url:
    if '--method' not in args or args[args.index('--method')+1]!='POST':sys.exit(2)
    payload=json.load(open(args[args.index('--body-file')+1]));variables=payload['variables']
    if 'search' in variables:
        after=variables.get('after');number=2 if after else 1
        node={'number':number,'title':'Synthetic PR','isDraft':False,'reviewDecision':'REVIEW_REQUIRED','updatedAt':'2026-10-31T22:00:00Z','repository':{'nameWithOwner':'example-org/example-repo'},'commits':{'nodes':[{'commit':{'statusCheckRollup':{'state':'FAILURE'}}}]}}
        body={'data':{'search':{'issueCount':2,'pageInfo':{'hasNextPage':not bool(after),'endCursor':'cursor-next' if not after else None},'nodes':[node]}}}
        if os.environ.get('PC_TEST_CHECKS_DENIED'):
            if 'statusCheckRollup' in payload['query']:body={'data':None,'errors':[{'message':'Synthetic Checks permission denied'}]}
            else:node.pop('commits',None)
    else:body={'data':{'user':{'contributionsCollection':{'contributionCalendar':{'weeks':[{'contributionDays':[{'date':'2026-10-30','contributionCount':2},{'date':'2026-10-31','contributionCount':4}]}]}}}}}
elif '/v9/projects/' in url:body={'id':'prj_example'}
elif '/v6/deployments?' in url:
    body={'deployments':[{'uid':'dpl_failed','url':'failed.example.com','target':'production','state':'ERROR','createdAt':2000,'meta':{'githubCommitSha':'failed'}},{'uid':'dpl_live','url':'live.example.com','target':'production','state':'READY','createdAt':1000,'ready':1500,'meta':{'githubCommitSha':'live'}}]}
elif '/v13/deployments/' in url:body={'url':'live.example.com','target':'production','readyState':'READY','alias':['app.example.com'],'meta':{'githubCommitSha':'live'}}
else:
    mode=os.environ.get('PC_TEST_HEALTH','ok')
    if mode=='timeout':sys.exit(3)
    if mode=='tls-invalid':verified=False
    if mode=='server-error':status=503
    if mode=='auth-error':status=403
    body={'status':'ok'} if mode!='html' else '<html>Login</html>'
body=json.dumps(body) if not isinstance(body,str) else body
print(json.dumps({'status':status,'tls_verified':verified,'hostname_verified':verified,'body':body}))
sys.exit(0 if status<300 else 22)
'''


class NativeProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        harness = cls.root / 'harness.c'
        harness.write_text(HARNESS)
        cls.binary = cls.root / 'providers-test'
        provider_object = cls.root / 'providers.o'
        flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread', '-I', str(NATIVE)]
        subprocess.run(['cc', *flags, '-Dfork=pc_checked_fork', '-c', str(NATIVE / 'standalone_providers.c'), '-o', str(provider_object)], check=True)
        subprocess.run(['cc', *flags, str(harness), str(provider_object), '-o', str(cls.binary)], check=True)
        helper = cls.root / 'https-fake'
        helper.write_text(FAKE_HTTPS)
        helper.chmod(0o700)
        cls.config = {
            'https_helper':str(helper),'ca_file':'unused-test-ca',
            'github_login':'example-user','github_org':'example-org','github_token_file':'unused-test-token',
            'vercel_scope':'example-team','vercel_project':'example-project','vercel_token_file':'unused-test-token',
            'production_alias':'app.example.com','health_url':'https://api.example.com/health',
        }
        cls.config_path = cls.root / 'config.json'
        cls.config_path.write_text(json.dumps(cls.config))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def invoke(self,*args):
        result=subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,check=True,timeout=15)
        return json.loads(result.stdout)

    def predicate(self,actual,expected):
        a=self.root/'actual.json';e=self.root/'expected.json'
        a.write_text(json.dumps(actual));e.write_text(json.dumps(expected))
        return self.invoke('predicate',a,e)['matches']

    def test_real_process_transport_paginates_and_preserves_failed_deploy(self):
        result=self.invoke('collect',self.config_path)
        self.assertEqual((result['prs'],result['total'],result['complete']),(2,2,1))
        self.assertFalse(result['prs_error'])
        self.assertEqual((result['garden'],result['garden_total']),(2,6))
        self.assertEqual((result['deploy'],result['serving'],result['live']),(3,1,0))
        self.assertEqual(result['health'],1)

    def test_absent_sources_make_no_requests(self):
        config=self.root/'empty.json';config.write_text('{}')
        result=self.invoke('collect',config)
        self.assertEqual(result['prs'],0)
        self.assertEqual(result['samples'],0)
        self.assertEqual(result['health'],0)

    def test_request_pipe_is_close_on_exec_before_fork(self):
        result=self.invoke('probe',self.config_path,'ok')
        self.assertEqual(result['samples'],2)

    def test_cloud_refresh_never_adds_health_samples(self):
        result=self.invoke('cloud',self.config_path)
        self.assertEqual(result['prs'],2)
        self.assertEqual(result['samples'],0)
        self.assertEqual(result['health'],0)

    def test_fine_grained_metadata_fallback_keeps_all_prs_and_unknown_ci(self):
        result=subprocess.run([str(self.binary),'collect',str(self.config_path)],capture_output=True,text=True,check=True,timeout=15,env=os.environ|{'PC_TEST_CHECKS_DENIED':'1'})
        data=json.loads(result.stdout)
        self.assertEqual((data['prs'],data['total'],data['complete']),(2,2,1))
        self.assertEqual(data['checks_available'],0)
        self.assertEqual(data['first_ci'],0)
        self.assertFalse(data['prs_error'])

    def test_local_connection_failure_is_unknown_not_downtime(self):
        result=self.invoke('probe',self.config_path,'timeout')
        self.assertEqual((result['health'],result['samples'],result['failures'],result['unknown']),(0,1,0,1))
        self.assertEqual(result['availability'],100)

    def test_equivalent_json_formatting_preserves_observation_window(self):
        before=self.root/'predicate-before.json';after=self.root/'predicate-after.json'
        before.write_text(json.dumps(self.config|{'health_expected':{'status':'ok'}},indent=2))
        after.write_text(json.dumps(self.config|{'health_expected':{'status':'ok'}},separators=(',',':')))
        self.assertEqual(self.invoke('reconfigure',before,after)['samples'],2)
        before.write_text(json.dumps(self.config|{'health_expected':{'status':'ok','detail':{}}},indent=2))
        after.write_text(json.dumps(self.config|{'health_expected':{'detail':{},'status':'ok'}},separators=(',',':')))
        # Unknown probes count too: requiring a nonexistent detail must not
        # erase their history simply because the predicate was reformatted.
        result=self.invoke('reconfigure',before,after)
        self.assertEqual(result['unknown'],2)

    def test_changed_health_predicate_or_endpoint_starts_new_window(self):
        for replacement in ({'health_expected':{'status':'different'}},{'health_url':'https://other.example.com/health'}):
            changed=self.root/'changed-health.json';changed.write_text(json.dumps(self.config|replacement))
            result=self.invoke('reconfigure',self.config_path,changed)
            self.assertEqual(result['samples']+result['unknown'],1)

    def test_explicit_server_failure_counts_observed_unavailability(self):
        result=self.invoke('probe',self.config_path,'server-error')
        self.assertEqual((result['health'],result['samples'],result['failures']),(3,2,1))
        self.assertEqual(result['availability'],50)

    def test_unverified_tls_response_cannot_be_healthy(self):
        result=self.invoke('probe',self.config_path,'tls-invalid')
        self.assertEqual(result['health'],0)
        self.assertEqual(result['samples'],1)

    def test_login_html_and_access_denials_are_unknown(self):
        for mode in ('html','auth-error'):
            result=self.invoke('probe',self.config_path,mode)
            self.assertEqual(result['health'],0)
            self.assertEqual(result['failures'],0)

    def test_health_predicate_requires_matching_types_and_nonempty_object(self):
        self.assertTrue(self.predicate({'status':'ok','detail':1},{'status':'ok'}))
        self.assertFalse(self.predicate({'status':True},{'status':'ok'}))
        self.assertFalse(self.predicate({'status':'ok'},{}))
        self.assertTrue(self.predicate({'ready':{'value':True}},{'ready':{'value':True}}))

    def test_malformed_health_json_never_matches(self):
        actual=self.root/'malformed.json';expected=self.root/'expected.json'
        expected.write_text('{"status":"ok"}')
        for text in ('{"status":"ok",junk}','{"status" "ok"}','{"status":"ok"} trailing'):
            actual.write_text(text)
            self.assertFalse(self.invoke('predicate',actual,expected)['matches'])

    def test_graphql_errors_do_not_turn_into_empty_success(self):
        body=self.root/'error.json';body.write_text(json.dumps({'data':None,'errors':[{'message':'synthetic permission failure'}]}))
        self.assertEqual(self.invoke('prs',body)['result'],-1)

    def test_rejects_insecure_health_configuration(self):
        config=self.root/'bad.json';config.write_text(json.dumps(self.config|{'health_url':'http://api.example.com/health'}))
        self.assertTrue(self.invoke('collect',config)['config_error'])


if __name__=='__main__':
    unittest.main()
